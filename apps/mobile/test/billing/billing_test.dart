// Customer billing (docs/billing.md): parsing and money, the repository and
// controller against a fake API, and the screens in English and Arabic from a
// 320 px phone at 200 percent text to 1440 px.
import 'dart:typed_data';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/billing/billing_data.dart';
import 'package:anum_mobile/features/billing/billing_screen.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/localization/billing_strings.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

const _gb = 1024 * 1024 * 1024;

Map<String, Object?> planJson(String id, String name, int price,
        {bool current = false, int? seats = 10}) =>
    {
      'id': id,
      'code': id,
      'name': name,
      'description': '$name plan for growing teams',
      'price_minor': price,
      'currency': 'SAR',
      'interval': 'month',
      'trial_days': 14,
      'per_seat': false,
      'limits': {
        'seats': seats,
        'workspaces': 3,
        'agent_runs_per_month': null,
        'storage_gb': 10,
        'messages_per_month': 5000,
      },
      'features': ['Email support', 'Shared workspaces'],
      'current': current,
    };

Map<String, Object?> invoiceJson(int n,
        {String status = 'open', bool overdue = false}) =>
    {
      'id': 'inv_$n',
      'number': 'INV-${n.toString().padLeft(6, '0')}',
      'status': status,
      'kind': 'subscription',
      'currency': 'SAR',
      'period_start': '2026-10-01T00:00:00Z',
      'period_end': '2026-11-01T00:00:00Z',
      'issued_at': '2026-10-01T00:00:00Z',
      'due_at': '2026-10-15T00:00:00Z',
      'subtotal_minor': 100000,
      'tax_rate_bp': 1500,
      'tax_country': 'SA',
      'tax_minor': 15000,
      'total_minor': 115000,
      'amount_paid_minor': status == 'paid' ? 115000 : 15000,
      'amount_credited_minor': 0,
      'amount_due_minor': status == 'paid' ? 0 : 100000,
      'overdue': overdue,
    };

class FakeBillingApi implements ApiTransport {
  bool subscribed = true;
  bool readOnly = false;
  bool overdue = false;
  bool deny = false;
  bool fail = false;
  int invoiceTotal = 25;
  final requests = <ApiRequest>[];

  static const _blocked = ApiResponse(statusCode: 409, body: {
    'error': {
      'code': 'conflict',
      'message':
          'The current usage does not fit that plan (seats 6 of 3). Remove what is over the limit first; nothing is deleted for you.',
      'details': [
        {'location': 'seats', 'message': '6 of 3', 'type': 'blocked_by'}
      ],
    }
  });

  Iterable<ApiRequest> to(String method, String path) => requests
      .where((r) => r.method == method && r.uri.path == '/api/v1/billing$path');

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    final path = request.uri.path.replaceFirst('/api/v1/billing', '');
    final query = request.uri.queryParameters;
    if (deny) {
      return const ApiResponse(statusCode: 403, body: {
        'error': {
          'code': 'forbidden',
          'message': 'Only a workspace owner or an organisation admin can.'
        }
      });
    }
    if (fail) {
      return const ApiResponse(
          statusCode: 503, body: {'detail': 'The billing service is down.'});
    }
    switch ((request.method, path)) {
      case ('GET', '/overview'):
        return ApiResponse(statusCode: 200, body: {
          'account': {
            'display_name': 'Tyre Shop',
            'contact_name': 'Sara Ali',
            'contact_email': 'billing@shop.example',
            'company_name': 'Tyre Shop LLC',
            'address_line1': 'King Fahd Road 1',
            'city': 'Riyadh',
            'country': 'SA',
            'vat_number': '300000000000003',
            'credit_balance_minor': 0,
            'state': readOnly ? 'read_only' : 'active',
          },
          'subscription': subscribed
              ? {
                  'id': 'sub_1',
                  'plan_id': 'plan_growth',
                  'plan_name': 'Growth',
                  'price_minor': 29900,
                  'currency': 'SAR',
                  'interval': 'month',
                  'per_seat': false,
                  'seats': 1,
                  'status': overdue ? 'past_due' : 'active',
                  'trial_end': null,
                  'current_period_end': '2026-11-01T00:00:00Z',
                  'cancel_at_period_end': false,
                  'pending_plan_id': null,
                }
              : null,
          'usage': {
            'period': '2026-10',
            'meters': [
              {'key': 'seats', 'used': 6, 'limit': 10, 'unit': 'seats'},
              {
                'key': 'workspaces',
                'used': 3,
                'limit': 3,
                'unit': 'workspaces'
              },
              {'key': 'agent_runs', 'used': 120, 'limit': null, 'unit': 'runs'},
              {
                'key': 'storage',
                'used': (2.5 * _gb).round(),
                'limit': subscribed ? 10 * _gb : null,
                'unit': 'bytes'
              },
              {
                'key': 'messages',
                'used': 900,
                'limit': 5000,
                'unit': 'messages'
              },
            ],
          },
          'state': readOnly ? 'read_only' : 'active',
          'open_balance_minor': overdue || readOnly ? 115000 : 0,
          'overdue': overdue || readOnly,
          'currency': 'SAR',
          'payment': {
            'bank_transfer': {
              'enabled': true,
              'bank_name': 'Riyad Bank',
              'account_name': 'ANUM',
              'iban': 'SA0380000000608010167519',
              'swift': 'RIBLSARI',
            },
            'card_enabled': false,
          },
          'issuer': {'name': 'ANUM', 'email': 'billing@anum.example'},
        });
      case ('GET', '/plans'):
        // A JSON list arrives from the HTTP transport wrapped in `data`.
        return ApiResponse(statusCode: 200, body: {
          'data': [
            planJson('plan_small', 'Small', 9900, seats: 3),
            planJson('plan_growth', 'Growth', 29900, current: true),
            planJson('plan_team', 'Team', 59900, seats: null),
          ]
        });
      case ('GET', '/subscription/preview'):
        final small = query['plan_id'] == 'plan_small';
        return ApiResponse(statusCode: 200, body: {
          'effective': small ? 'period_end' : 'now',
          'amount_minor': small ? 0 : 15000,
          'tax_minor': small ? 0 : 2250,
          'total_minor': small ? 0 : 17250,
          'credit_minor': 0,
          'blocked_by': small
              ? [
                  {'key': 'seats', 'used': 6, 'limit': 3, 'unit': 'seats'}
                ]
              : [],
          'currency': 'SAR',
        });
      case ('POST', '/subscription/change'):
        if (request.body?['plan_id'] == 'plan_small') return _blocked;
        return const ApiResponse(
            statusCode: 200, body: {'subscription': null, 'invoice': null});
      case ('POST', '/subscription/cancel' || '/subscription/reactivate'):
        return const ApiResponse(statusCode: 200, body: {'subscription': null});
      case ('GET', '/invoices'):
        final offset = int.parse(query['offset'] ?? '0');
        final limit = int.parse(query['limit'] ?? '20');
        var all = [
          for (var n = invoiceTotal; n >= 1; n--)
            invoiceJson(n,
                status: n.isEven ? 'paid' : 'open', overdue: n == invoiceTotal)
        ];
        if (query['status'] != null) {
          all = [
            for (final i in all)
              if (i['status'] == query['status']) i
          ];
        }
        if (query['q'] != null) {
          all = [
            for (final i in all)
              if ((i['number'] as String).contains(query['q']!)) i
          ];
        }
        return ApiResponse(statusCode: 200, body: {
          'items': all.skip(offset).take(limit).toList(),
          'total': all.length,
          'limit': limit,
          'offset': offset,
        });
    }
    if (request.method == 'GET' && path.startsWith('/invoices/inv_')) {
      final n = int.parse(path.substring('/invoices/inv_'.length));
      return ApiResponse(statusCode: 200, body: {
        ...invoiceJson(n, status: n.isEven ? 'paid' : 'open'),
        'bill_to': {
          'company_name': 'Tyre Shop LLC',
          'contact_email': 'billing@shop.example',
          'city': 'Riyadh',
          'vat_number': '300000000000003',
        },
        'issuer': {
          'issuer_name': 'ANUM',
          'issuer_email': 'billing@anum.example'
        },
        'bank_details': {
          'enabled': true,
          'bank_name': 'Riyad Bank',
          'account_name': 'ANUM',
          'iban': 'SA0380000000608010167519',
          'swift': 'RIBLSARI',
        },
        'lines': [
          {
            'description': 'Growth plan, October 2026',
            'quantity': 1,
            'unit_minor': 100000,
            'amount_minor': 100000,
            'kind': 'plan',
          }
        ],
        'payments': [
          {
            'amount_minor': 15000,
            'method': 'bank_transfer',
            'reference': 'INV-000001',
            'received_at': '2026-10-05T00:00:00Z',
            'has_proof': true,
          }
        ],
        'credit_notes': [],
      });
    }
    return const ApiResponse(statusCode: 404, body: {'detail': 'Not found'});
  }
}

class Harness {
  Harness._(this.api, this.repository);
  final FakeBillingApi api;
  final ApiBillingRepository repository;
  final saved = <String>[];
  final downloads = <String>[];

  static Future<Harness> create() async {
    final sessions = MemorySessionStore();
    await sessions.write(LocalSession.fromJson({
      'access_token': 'anum_local_test',
      'token_type': 'bearer',
      'expires_at': DateTime.now()
          .toUtc()
          .add(const Duration(hours: 1))
          .toIso8601String(),
      'context': {
        'tenant_id': 'tenant_test',
        'workspace_id': 'workspace_test',
        'user_id': 'user_test',
        'roles': ['owner'],
      },
    }));
    final api = FakeBillingApi();
    late final Harness harness;
    final repository = ApiBillingRepository(
      AnumApiClient(
          baseUri: Uri.parse('http://127.0.0.1:8000'),
          transport: api,
          sessions: sessions),
      download: (path) async {
        harness.downloads.add(path);
        return Uint8List.fromList('%PDF-1.4'.codeUnits);
      },
      save: (name, bytes) async {
        harness.saved.add(name);
        return true;
      },
    );
    harness = Harness._(api, repository);
    return harness;
  }
}

Future<BillingController> pump(WidgetTester tester, Harness harness,
    {Size size = const Size(390, 844),
    double textScale = 1,
    Locale locale = const Locale('en')}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  final controller = BillingController(harness.repository);
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.dark(),
    locale: locale,
    supportedLocales: const [Locale('en'), Locale('ar')],
    localizationsDelegates: const [
      AnumLocalizations.delegate,
      GlobalMaterialLocalizations.delegate,
      GlobalWidgetsLocalizations.delegate,
      GlobalCupertinoLocalizations.delegate,
    ],
    builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context).copyWith(
            disableAnimations: true, textScaler: TextScaler.linear(textScale)),
        child: child!),
    home: BillingScreen(controller: controller),
  ));
  await tester.pumpAndSettle();
  return controller;
}

Future<void> openTab(WidgetTester tester, String label) async {
  final tab = find.widgetWithText(Tab, label);
  // The tab bar scrolls at large text sizes.
  await tester.ensureVisible(tab);
  await tester.pumpAndSettle();
  await tester.tap(tab);
  await tester.pumpAndSettle();
}

Future<void> tapText(WidgetTester tester, String text) async {
  await tester.ensureVisible(find.text(text).first);
  await tester.pumpAndSettle();
  await tester.tap(find.text(text).first);
  await tester.pumpAndSettle();
}

void main() {
  test('money, storage and VAT rates read for people', () {
    expect(blMoney(115000, 'SAR', 'en'), contains('1,150.00'));
    expect(blMoney(115000, 'SAR', 'en'), contains('SAR'));
    expect(blMoney(1999, 'usd', 'en'), contains('19.99'));
    expect(blMoney(500, 'SAR', 'ar'), isNotEmpty);
    expect(blGigabytes((2.5 * _gb).round(), 'en'), '2.5');
    expect(blRate(1500), '15');
    expect(blRate(1550), '15.5');
    expect(blRate(1525), '15.25');
    const meter = BlMeter(key: 'seats', used: 6, limit: 3, unit: 'seats');
    expect(meter.ratio, 2);
    expect(const BlMeter(key: 'a', used: 1, unit: 'runs').ratio, isNull);
  });

  test('English and Arabic strings have the same keys and placeholders', () {
    expect(billingAr.keys.toSet(), billingEn.keys.toSet());
    final names = RegExp(r'\{(\w+)\}');
    for (final key in billingEn.keys) {
      Set<String> of(String text) =>
          names.allMatches(text).map((match) => match.group(1)!).toSet();
      expect(of(billingAr[key]!), of(billingEn[key]!), reason: key);
    }
  });

  testWidgets('overview shows the plan, usage, bank details and contact',
      (tester) async {
    final h = await Harness.create();
    await pump(tester, h);
    expect(find.text('Billing'), findsOneWidget);
    expect(find.text('Growth'), findsOneWidget);
    expect(find.text('Active'), findsOneWidget);
    expect(find.textContaining('299.00'), findsWidgets);
    expect(find.text('Renews on'), findsOneWidget);
    expect(find.text('Usage this month (2026-10)'), findsOneWidget);
    expect(find.text('6 of 10'), findsOneWidget);
    expect(find.text('120, unlimited'), findsOneWidget);
    expect(find.text('2.5 GB of 10.0 GB'), findsOneWidget);
    expect(find.byType(LinearProgressIndicator), findsNWidgets(4));
    await tester.ensureVisible(find.text('SA0380000000608010167519'));
    expect(find.text('Riyad Bank'), findsOneWidget);
    expect(find.byTooltip('Copy IBAN'), findsOneWidget);
    await tester.ensureVisible(find.text('billing@shop.example'));
    expect(find.text('Sara Ali'), findsOneWidget);
    expect(find.text('This organisation is read-only'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('read-only and overdue banners explain what to pay',
      (tester) async {
    final h = await Harness.create();
    h.api.readOnly = true;
    final controller = await pump(tester, h);
    expect(find.text('This organisation is read-only'), findsOneWidget);
    expect(find.textContaining('Pay the overdue invoice'), findsOneWidget);
    h.api
      ..readOnly = false
      ..overdue = true;
    await controller.load();
    await tester.pumpAndSettle();
    expect(find.text('This organisation is read-only'), findsNothing);
    expect(find.text('An invoice is overdue'), findsOneWidget);
    expect(find.text('Past due'), findsOneWidget);
  });

  testWidgets('no subscription, denied and error states', (tester) async {
    final h = await Harness.create();
    h.api.subscribed = false;
    final controller = await pump(tester, h);
    expect(find.text('Not on a plan'), findsOneWidget);
    expect(find.textContaining('billing@anum.example'), findsOneWidget);
    expect(find.text('Check again'), findsOneWidget);
    await openTab(tester, 'Plans');
    expect(find.textContaining('not on a plan yet'), findsOneWidget);
    // Nothing to change without a subscription.
    final choose = find.widgetWithText(OutlinedButton, 'Choose Team');
    await tester.ensureVisible(choose);
    expect(tester.widget<OutlinedButton>(choose).onPressed, isNull);

    h.api.deny = true;
    await controller.load();
    await tester.pumpAndSettle();
    expect(find.text('Billing is for billing admins'), findsOneWidget);
    expect(
        find.textContaining(
            'Only a workspace owner or an organisation administrator'),
        findsOneWidget);

    h.api
      ..deny = false
      ..fail = true;
    await controller.load();
    await tester.pumpAndSettle();
    expect(find.text("Couldn't load billing"), findsOneWidget);
    expect(find.textContaining('The billing service is down.'), findsOneWidget);
    h.api
      ..fail = false
      ..subscribed = true;
    await tester.tap(find.text('Try again'));
    await tester.pumpAndSettle();
    expect(find.text('Growth'), findsOneWidget);
  });

  testWidgets('invoices search, filter and page', (tester) async {
    final h = await Harness.create();
    await pump(tester, h);
    await openTab(tester, 'Invoices');
    expect(find.text('INV-000025'), findsOneWidget);
    expect(find.text('Overdue'), findsOneWidget);
    expect(find.text('20 of 25'), findsOneWidget);
    await tapText(tester, 'Load more');
    expect(find.text('25 of 25'), findsOneWidget);
    expect(find.text('Load more'), findsNothing);
    final offsets = h.api
        .to('GET', '/invoices')
        .map((r) => r.uri.queryParameters['offset'])
        .toList();
    expect(offsets, ['0', '20']);

    await tester.enterText(find.byType(TextField), 'INV-000003');
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.pumpAndSettle();
    expect(h.api.to('GET', '/invoices').last.uri.queryParameters['q'],
        'INV-000003');
    expect(find.text('1 of 1'), findsOneWidget);

    await tester.enterText(find.byType(TextField), '');
    await tester.tap(find.widgetWithText(ChoiceChip, 'Paid'));
    await tester.pumpAndSettle();
    final last = h.api.to('GET', '/invoices').last.uri.queryParameters;
    expect(last['status'], 'paid');
    expect(last.containsKey('q'), isFalse);
    expect(find.text('12 of 12'), findsOneWidget);

    await tester.enterText(find.byType(TextField), 'NOPE');
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.pumpAndSettle();
    expect(find.text('No invoices match'), findsOneWidget);
    await tapText(tester, 'Clear search and filters');
    expect(find.text('20 of 25'), findsOneWidget);
  });

  testWidgets('invoice detail shows VAT, totals, payments and saves the PDF',
      (tester) async {
    final h = await Harness.create();
    await pump(tester, h);
    await openTab(tester, 'Invoices');
    await tapText(tester, 'INV-000025');
    expect(find.text('Invoice INV-000025'), findsOneWidget);
    expect(find.text('Growth plan, October 2026'), findsOneWidget);
    expect(find.text('VAT 15% (SA)'), findsOneWidget);
    expect(find.textContaining('1,150.00'), findsWidgets);
    expect(find.textContaining('150.00'), findsWidgets);
    expect(find.text('Subtotal'), findsOneWidget);
    expect(find.text('Proof of payment on file'), findsOneWidget);
    expect(find.text('Transfer reference: INV-000025'), findsOneWidget);
    expect(find.text('Pay by card'), findsNothing);
    await tapText(tester, 'Save PDF');
    expect(h.downloads, ['/api/v1/billing/invoices/inv_25/pdf']);
    expect(h.saved, ['INV-000025.pdf']);
    expect(find.text('Invoice PDF saved'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('a plan the usage does not fit cannot be confirmed',
      (tester) async {
    final h = await Harness.create();
    await pump(tester, h);
    await openTab(tester, 'Plans');
    expect(find.text('Current plan'), findsOneWidget);
    await tapText(tester, 'Choose Small');
    expect(
        h.api
            .to('GET', '/subscription/preview')
            .single
            .uri
            .queryParameters['plan_id'],
        'plan_small');
    expect(find.text('Current usage does not fit this plan'), findsOneWidget);
    expect(find.textContaining('Seats: 6 of 3'), findsOneWidget);
    final confirm = find.widgetWithText(FilledButton, 'Change to Small');
    expect(tester.widget<FilledButton>(confirm).onPressed, isNull);
    await tester.tap(find.text('Not now'));
    await tester.pumpAndSettle();
    expect(h.api.to('POST', '/subscription/change'), isEmpty);

    // A plan that fits: the cost is shown and the change is sent.
    await tapText(tester, 'Choose Team');
    expect(find.text('The change starts now.'), findsOneWidget);
    expect(find.textContaining('172.50'), findsOneWidget);
    await tester.tap(find.widgetWithText(FilledButton, 'Change to Team'));
    await tester.pumpAndSettle();
    expect(h.api.to('POST', '/subscription/change').single.body,
        {'plan_id': 'plan_team'});
    expect(find.text('The plan is now Team.'), findsOneWidget);
  });

  testWidgets('a 409 refusal names what is over the limit', (tester) async {
    final h = await Harness.create();
    final controller = await pump(tester, h);
    final failure = await controller.change('plan_small');
    expect(failure, contains('seats 6 of 3'));
    await tester.pumpAndSettle();
  });

  testWidgets('cancel and reactivate ask first', (tester) async {
    final h = await Harness.create();
    await pump(tester, h);
    await tapText(tester, 'Cancel plan');
    expect(find.text('Cancel the plan?'), findsOneWidget);
    await tester.tap(find.text('Not now'));
    await tester.pumpAndSettle();
    expect(h.api.to('POST', '/subscription/cancel'), isEmpty);
    await tapText(tester, 'Cancel plan');
    await tester.tap(find.text('Cancel at period end'));
    await tester.pumpAndSettle();
    expect(h.api.to('POST', '/subscription/cancel'), hasLength(1));
    expect(
        find.text('The plan ends at the end of this period.'), findsOneWidget);
  });

  for (final (size, scale, locale) in [
    (const Size(320, 640), 2.0, const Locale('en')),
    (const Size(390, 844), 1.0, const Locale('en')),
    (const Size(390, 844), 2.0, const Locale('ar')),
    (const Size(768, 1024), 1.0, const Locale('en')),
    (const Size(1440, 900), 1.0, const Locale('ar')),
    (const Size(1440, 900), 1.0, const Locale('en')),
  ]) {
    testWidgets(
        'fits at ${size.width.toInt()} px, text x$scale, ${locale.languageCode}',
        (tester) async {
      final h = await Harness.create();
      h.api.readOnly = true;
      await pump(tester, h, size: size, textScale: scale, locale: locale);
      expect(tester.takeException(), isNull);
      final ar = locale.languageCode == 'ar';
      if (ar) {
        expect(find.text('الفوترة'), findsOneWidget);
        expect(Directionality.of(tester.element(find.byType(BillingScreen))),
            TextDirection.rtl);
      }
      await openTab(tester, ar ? 'الخطط' : 'Plans');
      expect(tester.takeException(), isNull);
      await tapText(tester, ar ? 'اختيار Small' : 'Choose Small');
      expect(tester.takeException(), isNull);
      await tapText(tester, ar ? 'ليس الآن' : 'Not now');
      await openTab(tester, ar ? 'الفواتير' : 'Invoices');
      expect(tester.takeException(), isNull);
      await tapText(tester, 'INV-000025');
      expect(tester.takeException(), isNull);
      expect(find.byType(InvoiceScreen), findsOneWidget);
    });
  }
}
